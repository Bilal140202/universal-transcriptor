"use client";

import { CheckCircle2, CircleDashed, Loader2, XCircle, AlertTriangle } from "lucide-react";
import type { StageRecord } from "@/lib/types";

const STAGE_LABELS: Record<string, string> = {
  acquisition: "Media acquisition",
  probe: "Media probe",
  audio: "Audio extraction",
  asr: "Speech recognition",
  qa_transcript: "Transcript QA",
  translation: "Context translation",
  terminology: "Terminology control",
  segmentation: "Subtitle segmentation",
  export: "Export formats",
  mux: "Subtitle mux (stream copy)",
};

const STAGE_DESC: Record<string, string> = {
  acquisition: "Adapter resolves the source, streams and verifies the real media",
  probe: "ffprobe builds the MediaManifest (streams, duration, integrity)",
  audio: "Timing-safe 16 kHz mono extraction from the best audio stream",
  asr: "ASR router picks provider/model; word timestamps + VAD",
  qa_transcript: "Hallucination + temporal validation on the raw transcript",
  translation: "Context windows + glossary → English (never per-line)",
  terminology: "TerminologyMemory consistency enforcement",
  segmentation: "Professional cue rebuild (CPL/CPS/min-max duration)",
  export: "SRT / WebVTT / ASS / JSON writers",
  mux: "Optional soft track attach via -c copy (never re-encode)",
};

function StageIcon({ status }: { status: StageRecord["status"] }) {
  switch (status) {
    case "ok":
      return <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0" />;
    case "running":
      return <Loader2 className="h-5 w-5 text-amber-300 shrink-0 animate-spin" />;
    case "failed":
      return <XCircle className="h-5 w-5 text-red-400 shrink-0" />;
    case "skipped":
      return <AlertTriangle className="h-5 w-5 text-zinc-500 shrink-0" />;
    default:
      return <CircleDashed className="h-5 w-5 text-zinc-600 shrink-0" />;
  }
}

export function StageTimeline({ stages }: { stages: StageRecord[] }) {
  const doneCount = stages.filter((s) => s.status === "ok").length;
  const pct = stages.length ? Math.round((doneCount / stages.length) * 100) : 0;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-xs text-zinc-400">
        <span>{doneCount} / {stages.length || 10} stages complete</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ol className="space-y-1.5">
        {stages.length === 0 && (
          <li className="text-sm text-zinc-500 py-6 text-center">
            Waiting for the engine to report its first stage…
          </li>
        )}
        {stages.map((s) => (
          <li
            key={s.name}
            className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
              s.status === "failed"
                ? "border-red-900/60 bg-red-950/30"
                : s.status === "running"
                  ? "border-amber-800/50 bg-amber-950/20"
                  : "border-zinc-800/80 bg-zinc-900/40"
            }`}
          >
            <StageIcon status={s.status} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <p className="text-sm font-medium text-zinc-100 truncate">
                  {STAGE_LABELS[s.name] ?? s.name}
                </p>
                {s.seconds != null && (
                  <span className="text-[11px] text-zinc-500 tabular-nums shrink-0">
                    {s.seconds}s
                  </span>
                )}
              </div>
              <p className="text-[11px] text-zinc-500 leading-snug">
                {s.error ?? STAGE_DESC[s.name] ?? ""}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
