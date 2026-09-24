"use client";

import { Badge } from "@/components/ui/badge";
import { ShieldCheck } from "lucide-react";
import type { Finding } from "@/lib/types";

const SEV_STYLE: Record<string, string> = {
  error: "bg-red-950/60 text-red-300 border-red-900",
  warn: "bg-amber-950/60 text-amber-300 border-amber-900",
  info: "bg-zinc-900 text-zinc-300 border-zinc-700",
};

export function FindingsPanel({ findings }: { findings: Finding[] }) {
  if (!findings.length) {
    return (
      <div className="flex flex-col items-center justify-center py-14 text-center">
        <ShieldCheck className="h-10 w-10 text-emerald-400 mb-3" />
        <p className="text-sm font-medium text-zinc-200">No QA findings</p>
        <p className="text-xs text-zinc-500 mt-1 max-w-sm">
          Hallucination, temporal, terminology and English-quality scans raised
          no issues on this run.
        </p>
      </div>
    );
  }
  const order = { error: 0, warn: 1, info: 2 } as const;
  const sorted = [...findings].sort((a, b) => order[a.severity] - order[b.severity]);
  return (
    <div className="space-y-2 max-h-[26rem] overflow-y-auto pr-1 custom-scroll">
      {sorted.map((f, i) => (
        <div
          key={`${f.code}-${f.segment_id ?? ""}-${i}`}
          className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="outline" className={`text-[10px] font-semibold uppercase tracking-wide ${SEV_STYLE[f.severity]}`}>
              {f.severity}
            </Badge>
            <span className="text-xs font-mono text-zinc-300">{f.code}</span>
            {f.segment_id && (
              <span className="text-[10px] text-zinc-600 font-mono">{f.segment_id}</span>
            )}
          </div>
          <p className="text-sm text-zinc-200 mt-1.5 leading-snug">{f.message}</p>
          {f.suggestion && (
            <p className="text-xs text-emerald-300/80 mt-1">→ {f.suggestion}</p>
          )}
        </div>
      ))}
    </div>
  );
}
