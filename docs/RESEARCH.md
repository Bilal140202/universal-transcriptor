# RESEARCH DIGEST

Research performed during Phase 0 of the build. Findings below directly shaped
the implementation.

---

## 1. Google Drive acquisition mechanics (validated live, 2026-09)

**Test case:** `https://drive.google.com/file/d/1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC/view?usp=drivesdk`

Observed behavior (measured, not assumed):

1. `GET https://drive.google.com/uc?export=download&id=<ID>` → one redirect to
   `https://drive.usercontent.google.com/download?id=<ID>&export=download`.
2. That endpoint answers **HTTP 200 with `Content-Type: text/html`** — a
   **virus-scan warning page**, not media. Naive downloaders would save this
   HTML as `video.mp4`. This is the exact failure mode the spec forbids.
3. The interstitial is a form: action
   `https://drive.usercontent.google.com/download` with hidden fields
   `id`, `export=download`, `confirm=t`, `uuid=<per-request uuid>`.
4. Re-requesting the action with those parameters streams the actual media.

**Identified file:** `BABYMONSTER CHOOM TOUR KYOCERA DAY2.mp4`, **2.0 GB** —
confirming large-file streaming + Range resume is a first-class requirement.

**Failure modes to detect** (strings/status in the HTML response):
- `download quota` / `too many users` → quota exceeded (retry later; never treat as media)
- `You need access` / `Request access` / `sign in` → permission denied
- Missing interstitial + HTML → private/deleted file
- `Content-Type: text/html` on the "media" response → interstitial not handled

**Design consequences** in `acquisition/gdrive.py`:
- Parse file IDs from all common URL shapes (`/file/d/<id>/`, `open?id=`,
  `uc?id=`, `id=` param, `drive.usercontent.google.com/download?id=`).
- Treat **any** `text/html` 200 response as a signal, never as media.
- Parse the confirmation form generically (action + hidden inputs), not by
  hard-coding Google's current parameter names.
- Stream to disk in chunks with Range resume; verify magic bytes + ffprobe after.

## 2. Agent study: `Bilal140202/ytagent`

Examined via GitHub API (MIT, Python, `main`). Architecture:

- **CLI-first orchestrator** walking a **13-method fallback chain** — if one
  acquisition method fails, the next tier is tried deterministically.
- **Verifier with 6-layer file integrity**: size ≥ 1 MB → magic bytes → ffprobe
  → duration > 0 → (further playability checks).
- **Truth Agent**: persists `truth.json` + `obs.jsonl`, ranking methods by
  observed success in the current environment — the system *learns* which
  strategy works where it runs.
- Headless-environment design: no cookies, no browser, no OAuth, no runtime LLM.
- Fixture-driven tests (`valid_5s.mp4`, `truncated.mp4`, `not_a_video.txt`).
- YouTube specifics: `android_vr` innertube client, BGutil POT provider for
  BotGuard attestation, SOCKS5/Invidious/Cobalt/GH-Actions relays.

**Principles adopted here** (independently implemented, not copied):
- Layered verification chain before any file is called "media" (`verify.py`,
  `probe.py`) — mirrors ytagent's Verifier philosophy.
- Adapter/method registry with structured `AcquisitionRecord` per attempt —
  mirrors the fallback-chain orchestrator pattern, generalized beyond YouTube.
- Fixture-style pure-logic tests that never need network or ML models.
- Environment capability introspection surfaced to the user (`transcriptor
  providers` CLI command).

**Not adopted:** YouTube bypass machinery (out of scope here; the `YouTubeAdapter`
delegates to `yt-dlp` when installed).

## 3. Agent study: `Bilal140202/xagent`

`GET /repos/Bilal140202/xagent` → **404 Not Found** via the authenticated API:
the repository is either private under a different token scope, renamed, or not
yet created. Its principles could not be studied in this phase. The adapter
boundary keeps this non-blocking: when the repo becomes visible, its URL
resolution/session strategy can be absorbed as another `SourceAdapter` without
touching the engine.

## 4. ASR landscape (decision digest)

Full benchmarking is ongoing work; the router ships with the current consensus
and is provider-abstracted so results replace defaults without code changes.

| System | Strengths | Watch-outs | License |
|---|---|---|---|
| faster-whisper (CTranslate2) | 4×+ faster than openai/whisper, int8 CPU, word timestamps, VAD, good multilingual coverage | inherits Whisper weaknesses (repetition loops on silence) | MIT |
| WhisperX | word-accurate alignment via wav2vec2/CTC forced alignment + pyannote diarization | extra dependency weight; HF token for diarization models | BSD-2 (non-commercial components in diarization path) |
| whisper.cpp | CPU-only edge, GGML quantization | weaker tooling for word timing | MIT |
| NVIDIA NeMo | strong per-language SOTA (esp. CJK), streaming | heavyweight; per-language model zoo | Apache-2.0 (model-specific) |
| SeamlessM4T / M4T v2 | speech→text translation directly, broad coverage | translation quality behind dedicated ASR+MT for many pairs | CC-BY-NC (models) — **non-commercial** |
| Paraformer / SenseVoice | excellent Mandarin, fast | narrow language scope | model-specific |
| wav2vec2 / WavLM | forced-alignment backbone (timing, not ASR primary) | no native punctuation | model-specific |

**Default routing:** faster-whisper `large-v3` (GPU, accuracy mode) /
`small` or `distil-large-v3` (CPU or long media) + WhisperX alignment when word
precision is required. Language-optimized overrides live in `ASRRouter`
config, not hard-code.

## 5. Translation landscape (decision digest)

| Option | Verdict |
|---|---|
| NLLB-200 (CTranslate2) | default local MT: 200 languages, decent context-free sentence quality, commercial-friendly (CC-BY-NC weights — check per deployment) |
| M2M100 | older NLLB fallback; weaker |
| OPUS-MT / Marian | per-pair quality can beat NLLB, but per-pair model management |
| Local LLM (Qwen/Llama/Gemma) | best context handling (window + glossary), slower; primary for quality mode; requires strict JSON I/O to keep 1:1 mapping |
| Cloud APIs | optional, opt-in only (spec: cloud never mandatory) |

**Architecture consequence:** translation runs over **context windows**
(prev/next segments + speaker + glossary), never isolated lines; output is
mapped 1:1 to segment ids; `TerminologyMemory` is enforced post-hoc with
findings, not silent rewrites.

## 6. Subtitle rules (professional baseline)

Defaults in `subtitles/segmentation.py`, all configurable:

- Max 42 chars/line, max 2 lines (Netflix-style); line balancing on split.
- Reading speed ≤ 20 chars/sec (17 ideal); min duration ≈ 5/6 s (1.0 s default
  here); max duration 7.0 s; minimum gap ≈ 2 frames (0.084 s @24fps).
- Split at sentence boundaries first, then clause/phrase boundaries; never split
  mid-possessive, before punctuation, or across a speaker change.
- Cue end may extend into silence to satisfy CPS, but never past the next cue's
  start or past media duration (temporal scan enforces).

## 7. Hallucination defense heuristics implemented

- n-gram repetition loops (≥3 identical consecutive n-grams) → flag
- `no_speech_prob` high + text present → flag
- `avg_logprob` → confidence; extreme `compression_ratio` → flag
- negative/zero durations, overlap, cues beyond media duration → temporal flags
- low-confidence segments surfaced for human review, never auto-deleted
