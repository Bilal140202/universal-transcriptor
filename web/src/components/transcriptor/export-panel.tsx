"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Copy, Download, FileJson, Film, FileText, ListChecks } from "lucide-react";
import { toast } from "@/hooks/use-toast";
import { fmtBytes, type JobDetail } from "@/lib/types";

const FORMATS: Array<{ fmt: string; label: string; icon: React.ReactNode; desc: string }> = [
  { fmt: "srt", label: "SubRip (.srt)", icon: <FileText className="h-4 w-4" />, desc: "Universal subtitle format" },
  { fmt: "vtt", label: "WebVTT (.vtt)", icon: <FileText className="h-4 w-4" />, desc: "HTML5 <track> / web players" },
  { fmt: "ass", label: "Advanced SSA (.ass)", icon: <FileText className="h-4 w-4" />, desc: "Styled subtitles / burn-in" },
  { fmt: "json", label: "Transcript (.json)", icon: <FileJson className="h-4 w-4" />, desc: "Words, segments, cues, QA" },
];

export function ExportPanel({ job }: { job: JobDetail }) {
  const artifacts = job.report?.artifacts ?? {};
  const muxed = artifacts["muxed"];

  function copySrt() {
    const url = `/api/jobs/${job.meta.id}/download?fmt=srt`;
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error("not ready"))))
      .then((t) => navigator.clipboard.writeText(t))
      .then(() => toast({ title: "SRT copied to clipboard" }))
      .catch(() => toast({ title: "SRT not available yet", variant: "destructive" }));
  }

  return (
    <div className="space-y-5">
      <div className="grid sm:grid-cols-2 gap-2.5">
        {FORMATS.map(({ fmt, label, icon, desc }) => {
          const size = job.files?.[`transcript.${fmt}`];
          const ready = !!size;
          return (
            <a
              key={fmt}
              href={`/api/jobs/${job.meta.id}/download?fmt=${fmt}`}
              className={`group rounded-lg border px-3.5 py-3 flex items-center gap-3 transition-colors ${
                ready
                  ? "border-zinc-800 bg-zinc-900/50 hover:border-emerald-800 hover:bg-emerald-950/20"
                  : "border-zinc-900 bg-zinc-950/50 opacity-50 pointer-events-none"
              }`}
            >
              <span className="text-emerald-400">{icon}</span>
              <span className="flex-1 min-w-0">
                <span className="block text-sm font-medium text-zinc-100 truncate">{label}</span>
                <span className="block text-[11px] text-zinc-500 truncate">
                  {ready ? `${desc} · ${fmtBytes(size)}` : "pending pipeline"}
                </span>
              </span>
              <Download className="h-4 w-4 text-zinc-600 group-hover:text-emerald-400" />
            </a>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={copySrt}
          className="border-zinc-800 bg-zinc-900 text-zinc-300 hover:text-zinc-100 text-xs">
          <Copy className="h-3.5 w-3.5 mr-1.5" /> Copy SRT
        </Button>
        <a href={`/api/jobs/${job.meta.id}/download?fmt=report`}>
          <Button size="sm" variant="outline"
            className="border-zinc-800 bg-zinc-900 text-zinc-300 hover:text-zinc-100 text-xs">
            <ListChecks className="h-3.5 w-3.5 mr-1.5" /> QA report
          </Button>
        </a>
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3.5">
        <div className="flex items-center gap-2 mb-1">
          <Film className="h-4 w-4 text-emerald-400" />
          <p className="text-sm font-medium text-zinc-100">Full media delivery</p>
        </div>
        {muxed ? (
          <p className="text-xs text-zinc-400 leading-relaxed">
            Soft-subtitled copy produced via <span className="font-mono text-emerald-300">-c copy</span> —
            bit-for-bit stream copy, zero re-encode, zero generation loss.
          </p>
        ) : job.meta.mux ? (
          <p className="text-xs text-zinc-500">Mux stage pending…</p>
        ) : (
          <p className="text-xs text-zinc-500 leading-relaxed">
            Stream-copy muxing was not requested for this run. Enable
            &ldquo;Attach soft subtitle track&rdquo; when submitting to receive a
            copy of the video carrying the English subtitle track — the video
            stream itself is never re-encoded.
          </p>
        )}
        {job.report?.artifacts?.["media"] && (
          <Badge variant="outline" className="mt-2 border-zinc-800 text-zinc-500 text-[10px] font-mono">
            media: {job.report.artifacts["media"].split("/").pop()}
          </Badge>
        )}
      </div>
    </div>
  );
}
