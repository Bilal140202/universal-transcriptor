# web/ — Frontend (planned phase)

The polished review UI lives here (spec §19–20):

1. **Submit** — paste a URL (Google Drive, direct HTTP, YouTube) or drop a file.
2. **Watch** — stage-by-stage progress from the same `report.json` the CLI emits;
   no hidden server-side state.
3. **Review** — video preview with the generated subtitle track, side-by-side
   source/English with word timing, QA findings inline (hallucination flags,
   terminology inconsistencies, reading-speed warnings).
4. **Edit** — fix text, adjust cue timing within the authoritative bounds,
   add terminology overrides (fed back into `TerminologyMemory`).
5. **Export** — download SRT/VTT/ASS/JSON or the stream-copy muxed file.

Technology target: Next.js + shadcn/ui frontend; FastAPI (or Next.js API routes)
backend that wraps `transcriptor.pipeline.PipelineOrchestrator`.

Not started in this commit — the engine CLI/API surface above is the contract
the UI will consume.
