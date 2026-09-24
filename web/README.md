# web/ — Review UI (implemented)

A polished, zero-config Next.js 16 review UI over the engine. The UI keeps the
engine's "no hidden state" principle: every job's truth lives on disk in its
run directory (`report.json`, `transcript.json`, `edits.json`), and the API
layer only reads those files or shells out to the `transcriptor` CLI.

## Features (spec §19–20)

1. **Submit** — paste a Google Drive share link, a direct HTTP URL, or a local
   media path; pick accuracy tier, source language, optional prefix excerpt
   (timestamps stay 1:1), terminology overrides, and stream-copy muxing.
2. **Watch** — live stage-by-stage pipeline progress, polled straight from the
   engine's incrementally-written `report.json`.
3. **Review** — in-browser video preview (lightweight derived clip; the
   original is never re-encoded for delivery), VTT subtitle track overlay,
   side-by-side source/English segment editor, QA findings inline.
4. **Edit** — fix English text per segment → `edits.json` → `transcriptor
   reexport` rebuilds SRT/VTT/ASS/JSON. Timing is never rewritten.
5. **Export** — SRT / WebVTT / ASS / JSON downloads, QA report, stream-copy
   muxed media when requested.

## Layout

```
web/
├── src/app/page.tsx                single-page UI (submit · watch · review · export)
├── src/app/api/jobs/…              job create/list/detail/transcript/edits/download
├── src/app/api/media/route.ts      Range-aware video streaming (preview or source)
├── src/components/transcriptor/    submit form, stage timeline, review/findings/export panels
├── src/lib/engine.ts               server-side bridge: spawns `python -m transcriptor`
├── src/lib/types.ts                shared UI types
└── mini-services/mt-llm/           optional OpenAI-compatible LLM translation proxy
```

## Running

Prereqs: Node 20+/Bun, Python 3.10+ with the engine installed
(`pip install -e "../[asr,mt]"` from the repo root), ffmpeg on PATH.

```bash
cd web
npm install

# Point the bridge at your engine checkout and run dir (defaults shown)
export TRANSCRIPTOR_ENGINE_DIR=/opt/universal-transcriptor
export TRANSCRIPTOR_JOBS_DIR=/var/lib/transcriptor/jobs

# Optional: LLM translation via an OpenAI-compatible endpoint
# (any local llama.cpp / Ollama / vLLM server works)
bun run ../web/mini-services/mt-llm/index.ts &   # sandbox SDK proxy, :3030
export TRANSCRIPTOR_LLM_BASE_URL=http://127.0.0.1:3030/v1
export TRANSCRIPTOR_LLM_MODEL=z-ai

npm run dev          # http://localhost:3000
```

Environment reference:

| Variable | Purpose |
|---|---|
| `TRANSCRIPTOR_ENGINE_DIR` | engine checkout (must contain `src/transcriptor`) |
| `TRANSCRIPTOR_ENGINE_SRC` | override the import path directly |
| `TRANSCRIPTOR_JOBS_DIR` | where run directories are created |
| `TRANSCRIPTOR_LLM_BASE_URL` / `_MODEL` / `_API_KEY` | OpenAI-compatible translation endpoint |
| `TRANSCRIPTOR_MT_DTYPE` | NLLB torch fallback dtype (`bfloat16` recommended) |
| `TRANSCRIPTOR_NLLB_CT2_DIR` | CTranslate2 NLLB model dir (int8, ~650 MB RAM) |
| `TRANSCRIPTOR_CHUNK_PACING_S` | pause between ranged download chunks |
| `TRANSCRIPTOR_ACQ_STRATEGY` | `paced` (default) or `parallel` |

## Preview clip note

The review player streams a derived ~60 s preview (`preview.mp4`, generated
on demand). This is the ONLY re-encode in the product and exists purely for
playback convenience — subtitle delivery always goes through the stream-copy
mux (`-c copy`), and the authoritative media is never modified.
