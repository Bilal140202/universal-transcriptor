# SPEC — Universal Video → English Transcriptor

> Engineering specification, imported from the project brief. The brief's pasted
> source was truncated mid-way; the tail sections were completed faithfully and
> are marked *(reconstructed)*.

## 1. Mission

Build a production-grade, research-driven, cloud-executable system that accepts a
video/audio source from a user — initially including a Google Drive share URL —
acquires the actual media file, processes speech in **any supported spoken
language**, translates the spoken content into **high-quality English**, preserves
**exact temporal alignment**, reconstructs professionally formatted English
subtitles/transcripts, validates the result, and exposes the entire workflow
through a polished frontend requiring **no external setup** from the user.

The first test input:

```
https://drive.google.com/file/d/1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC/view?usp=drivesdk
```

It is the initial real-world test case only. The system must **not** be designed
around this one video. It must become a **general-purpose media → English
transcription and translation engine**.

## 2. Absolute principle

DO NOT build a simple `video → Whisper → translate → SRT` pipeline. Build:

```
SOURCE → MEDIA ACQUISITION → MEDIA INSPECTION → MEDIA NORMALIZATION
→ AUDIO EXTRACTION → AUDIO QUALITY ANALYSIS → SPEECH DETECTION
→ LANGUAGE DETECTION → SPEAKER ANALYSIS → HIGH-QUALITY ASR
→ WORD-LEVEL ALIGNMENT → SEGMENT RECONSTRUCTION → CONTEXT BUILDING
→ TRANSLATION → TERMINOLOGY CONTROL → TRANSLATION QA → SPELLING/GRAMMAR QA
→ TEMPORAL VALIDATION → SUBTITLE SEGMENTATION → FORMATTING → QUALITY SCORING
→ HUMAN REVIEW UI → EXPORT
```

And: **video must never be re-encoded just to produce a subtitle file.**
When possible: stream-copy the video/audio and add a new subtitle track.
Burned-in subtitles require an explicitly requested separate render pipeline.

## 3. The real product

The product is the **Universal Media Transcription Engine**. It should eventually
accept: Google Drive URLs, direct video/audio URLs, supported cloud storage URLs,
uploaded files, local files, public media URLs, video platforms where acquisition
is legally and technically supported, subtitle files, audio-only and video-only
files.

The system must determine: what is this source? how do I access it? what is the
actual media? which codec/container? which audio/subtitle streams exist? which
language is spoken? how many speakers? the best ASR / alignment / translation
strategy? how English should be segmented? how timing is preserved? how the
result is verified?

## 4. Media acquisition (first-class requirement)

The user pastes a `https://drive.google.com/...` link and the system acquires the
actual media automatically. Do **not** assume HTTP-downloading the visible Drive
page is enough. Research Drive sharing/download mechanics and handle safely:
public files, share-link files, large files, confirmation/interstitial pages,
redirect chains, virus-scan confirmation pages, quota restrictions, expired URLs,
inaccessible files, HTML-instead-of-media, partial downloads, resumable
downloads, content-length, range requests, authentication requirements.

The system must inspect every response before treating it as media. **Never save
an HTML error page as `video.mp4` and pretend it downloaded successfully.**

### 4.1 Media acquisition agent

A dedicated abstraction, `MediaSourceResolver`:

```
USER URL → SOURCE CLASSIFICATION → DOMAIN DETECTION → SOURCE ADAPTER
→ ACCESS STRATEGY → DOWNLOAD → VERIFY → MEDIA PROBE → LOCAL OBJECT
```

Adapters (pluggable): `GoogleDriveAdapter`, `DirectHTTPAdapter`, `YouTubeAdapter`,
`XAdapter`, `GenericVideoAdapter`, `CloudStorageAdapter`, `FutureAdapter`.
Acquisition logic is never hard-coded into the transcription engine.

### 4.2 Acquisition safety

Verify: HTTP status, Content-Type, Content-Length, magic bytes, container
signature, file extension, ffprobe result, download completeness, checksum where
possible. Every download records: source URL, resolved URL, source type, file
name, size, content type, checksum, download start/end, duration, codecs,
streams, success/failure, error.

### 4.3 Large file handling

Never load entire videos into RAM. Support streaming/chunked download, HTTP
Range, resumable download, stream verification, partial recovery, disk-backed
processing. A 10 GB video must not crash the service because someone called
`read_all()`.

## 5. Media probe

Use a serious probing stage (FFprobe or equally justified): container, duration,
video/audio/subtitle streams, codec, sample rate, channels, bit depth, frame
rate, resolution, bitrate, language metadata, rotation, HDR, color space,
variable frame rate. Produce a `MediaManifest`:

```
MediaManifest
├── container          ├── duration           ├── video_streams
├── audio_streams      ├── subtitle_streams   ├── chapters
├── metadata           ├── language_hints     ├── codec_information
└── integrity
```

### 5.1 Input media variety

Research and support where practical: MP4, MKV, MOV, WebM, AVI, MPEG, TS, M4V,
FLV, MP3, WAV, AAC, M4A, FLAC, OGG, OPUS. Do not promise unsupported formats.
Build capability detection; if media cannot be decoded, explain exactly why.

## 6. Audio extraction

Intelligently choose the best audio stream (language, codec, channels, metadata,
bitrate, quality). Allow automatic and manual selection; if the selected audio is
poor, research whether another stream is better. Potential preprocessing:
resampling, mono conversion, loudness normalization, noise suppression, voice
isolation, silence removal for ASR only, channel mixing, high/low-pass where
justified.

**CRITICAL:** do not modify the authoritative timing model because preprocessing
changes duration. ASR preprocessing audio may be *derived*; original media
timestamps remain authoritative.

## 7. Language detection

Automatic spoken-language detection across Japanese, Korean, Chinese Mandarin,
Cantonese (where supported), English, Spanish, French, German, Italian,
Portuguese, Russian, Arabic, Hindi, Urdu, Bengali, Thai, Vietnamese, Indonesian,
Turkish, etc. Never hard-code only Japanese/Korean/Chinese. New languages are
added through model/provider adapters.

## 8. Multilingual ASR

Research deeply: OpenAI Whisper, whisper.cpp, faster-whisper, CTranslate2,
WhisperX, Whisper large-v3 and variants, distilled models, NVIDIA NeMo ASR,
SeamlessM4T, wav2vec2, WavLM, Paraformer, SenseVoice, Qwen ASR, and other current
multilingual systems. Do **not** automatically choose Whisper — benchmark
alternatives on: language coverage, accuracy, WER/CER, code-switching, noise
robustness, speaker changes, punctuation, Japanese segmentation, Korean spacing,
Chinese segmentation, proper nouns, technical vocabulary, speed, VRAM, CPU
requirements, word timestamps, license, commercial use, cloud requirements, local
deployment.

### 8.1 ASR provider abstraction

`ASRProvider` with providers: `WhisperProvider`, `FasterWhisperProvider`,
`WhisperCppProvider`, `WhisperXProvider`, `AlternativeASRProvider`,
`FutureASRProvider`. Selection modes: `BEST_AVAILABLE`, `FASTEST`,
`HIGHEST_ACCURACY`, `LOW_MEMORY`, `CPU_ONLY`, `GPU`, `LANGUAGE_OPTIMIZED`. No
model is hard-coded into the application.

### 8.2 ASR router

`ASRRouter` inputs: language, audio quality, duration, GPU availability, CPU,
RAM, VRAM, speaker count, expected accuracy, word timestamp requirement.
Outputs: model, backend, compute type, batch size, VAD strategy, alignment
strategy. Examples: Japanese + clean audio + GPU → high-accuracy multilingual
model; long media on low-resource device → efficient model; very noisy audio →
preprocessing + stronger ASR; high-precision subtitle timing → ASR + forced
alignment.

## 9. Transcription architecture

Never treat transcription as `audio → giant text string`. Produce a
`TranscriptDocument`: `document_id`, `source_media_id`, `language`, `segments[]`,
`speakers[]`, `words[]`, `confidence`, `timing`, `metadata`. Each word: `text`,
`start`, `end`, `confidence`, `speaker`, `language`. Each segment: `segment_id`,
`start`, `end`, `text`, `words`, `speaker`, `language`, `confidence`. This
temporal structure is **authoritative**.

## 10. Word alignment (critical)

Research: Whisper timestamps, WhisperX, forced alignment, wav2vec2/CTC
alignment, Montreal Forced Aligner, stable-ts. Goal: very accurate word-level
timing. Do not blindly trust raw Whisper segment timestamps — native Whisper
timing is not always precise enough for word-level synchronization (WhisperX adds
word-level alignment and diarization). Build `AlignmentProvider`:
`NativeTimestamp`, `WhisperXAlignment`, `StableTS`, `ForcedAlignment`,
`FutureAlignment` — and benchmark them.

## 11. Translation engine

The system must **not** translate each subtitle line independently — that breaks
grammar, loses context, and produces inconsistent names and unnatural English.
Translation operates over contextual windows while preserving original timing
anchors.

Research: NLLB, M2M100, SeamlessM4T, Marian, OPUS-MT, Llama-based local
translation, Qwen, Gemma, other local multilingual models, cloud APIs where
explicitly configured. Compare accuracy, context, speed, hardware, language
coverage, proper-noun handling, format preservation, local operation, license.
Create `TranslationProvider` with modes `LOCAL_NLLB`, `LOCAL_LLM`,
`LOCAL_SEAMLESS`, `CLOUD_PROVIDER`, `HYBRID`, `AUTO`. Cloud APIs are never
mandatory.

### 11.1 Context-aware translation

Translation understands: previous segment, current segment, next segment,
speaker, scene/topic, proper nouns, terminology, document title, known names,
repeated phrases, style. Example: 「彼はそれをやった。」 must not be translated from
the isolated sentence when context determines who "he" is or what "it" means.

### 11.2 Terminology management

`TerminologyMemory` covering proper names, group names, company names, technical
terms, character names, place names, abbreviations, slang, brand names, song
names. Example: "YG" must consistently remain "YG" — never "Y.G." or "Why Gee".
Terminology is learned from metadata, prior segments, user corrections, known
entities, custom glossary, and source-language repetition. Track: source term,
English term, confidence, context, occurrences, user override. The same name is
never translated five different ways.

## 12. English quality pipeline *(reconstructed tail)*

Translation output passes through: spelling, grammar, punctuation,/
capitalization, proper nouns, terminology, context, readability, subtitle
length, timing. Do **not** blindly "polish" the translation — English must
remain faithful to the original meaning.

## 13. Multi-pass QA

Research and implement:

| Pass | Stage |
|---|---|
| 1 | ASR |
| 2 | Alignment |
| 3 | Translation |
| 4 | English normalization |
| 5 | Quality check |
| 6 | Timing check |
| 7 | Terminology check |
| 8 | Source/translation consistency check |
| 9 | Second-model review (optional) |

Do not add LLM calls merely for appearance — benchmark whether each additional
pass improves accuracy.

### 13.1 Translation review model

Where an LLM reviews, it inspects source language, source text, English
translation, context, timing, terminology, and answers: Is meaning preserved? Is
anything omitted or invented? Is the English grammatical? Are names and
punctuation correct? Is style appropriate? Too literal or too free? Does it
contradict neighboring segments? The reviewer outputs **structured findings** and
never silently rewrites everything.

## 14. Hallucination defense

ASR hallucination is a serious problem. Research and apply: VAD, no-speech
probability, repetition detection, confidence, temperature fallback, compression
ratio, language consistency, segment plausibility, silence/audio-energy
correlation. Detect repeated phrases, invented speech during silence, impossible
timestamps, extremely low-confidence segments, language-switching anomalies.
**Mark suspicious segments; do not silently delete uncertain content.**

## 15. Speaker diarization

Research: pyannote, WhisperX, NVIDIA NeMo, other diarization systems. If multiple
speakers exist, label Speaker 1/2/3 and preserve speaker association. Never
promise real names unless identity is actually known. Translation context
includes speaker information when useful (conversational Q&A structure).

## 16. Subtitle segmentation

This is **not** merely "one ASR segment = one subtitle". Build a subtitle
segmentation engine using professional rules: characters per line, characters per
second, minimum/maximum duration, reading speed, punctuation, sentence
boundaries, speaker changes, shot changes, phrase and semantic boundaries, line
balancing. The system must produce readable English subtitles.

## 17. Timeline preservation

**Absolute requirement: translation must not destroy timing.** Maintain for every
cue: `source_audio_start`, `source_audio_end`, and the corresponding translated
span. Re-segmentation may regroup text, but every output cue maps back to
authoritative source-media time. All validation compares output timing against
the `MediaManifest` duration and the original word timings.

## 18. Export *(reconstructed)*

- SRT, WebVTT, ASS (minimal styling), and a rich JSON transcript export.
- **Default video path:** mux the subtitle track with stream copy
  (`-c copy -c:s mov_text` for MP4); the video is never re-encoded for subtitles.
- Burn-in is a separate, explicit, user-requested render pipeline.

## 19. Quality scoring & human review UI *(reconstructed)*

Every run produces a quality report: per-stage timings, per-segment confidence,
QA findings (code, severity, location), terminology consistency summary, and a
weighted overall score. The frontend exposes the review surface: video preview
with the generated track, side-by-side source/English with timing, flagged
segments, terminology overrides, and re-export after edits.

## 20. Frontend *(reconstructed)*

A polished web UI requiring **no external setup** from the user: paste a URL →
watch stage-by-stage progress → review/edit results in the browser → download
SRT/VTT/JSON or the muxed file. The UI consumes the same pipeline artifacts the
CLI produces; no hidden server-side state.

## 21. Relationship to existing agents

Before designing the downloader, study `bilal140202/ytagent` and
`bilal140202/xagent`: architecture, downloader strategy, URL resolution, media
discovery, metadata extraction, session handling, browser/network strategy, error
handling, API boundaries, reusable components, weaknesses, licensing. Extract
reusable engineering principles; extend an existing agent only if it extends
cleanly; otherwise build a new independent engine (`media-agent` /
`mediafetch-agent` / `media-source-agent` / `media-acquisition-engine`). Do not
blindly copy, and do not force unrelated responsibilities into `ytagent`/`xagent`.
See `RESEARCH.md` for the study results and what was adopted.

