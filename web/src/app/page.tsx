"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Activity, CircleDot, Clapperboard, Github, Languages, Loader2,
  ShieldAlert, Sparkles,
} from "lucide-react";
import { SubmitForm, type SubmitFormValues } from "@/components/transcriptor/submit-form";
import { StageTimeline } from "@/components/transcriptor/stage-timeline";
import { FindingsPanel } from "@/components/transcriptor/findings-panel";
import { ReviewPanel } from "@/components/transcriptor/review-panel";
import { ExportPanel } from "@/components/transcriptor/export-panel";
import type { JobDetail, JobSummary, TranscriptView } from "@/lib/types";

const STATUS_STYLE: Record<string, { cls: string; label: string }> = {
  running: { cls: "bg-amber-950/70 text-amber-300 border-amber-800", label: "Running" },
  ok: { cls: "bg-emerald-950/70 text-emerald-300 border-emerald-800", label: "Complete" },
  partial: { cls: "bg-emerald-950/70 text-emerald-300 border-emerald-800", label: "Partial" },
  failed: { cls: "bg-red-950/70 text-red-300 border-red-900", label: "Failed" },
};

export default function Home() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [transcript, setTranscript] = useState<TranscriptView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshJobs = useCallback(async () => {
    try {
      const res = await fetch("/api/jobs", { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setJobs(data.jobs ?? []);
        return data.jobs as JobSummary[];
      }
    } catch { /* transient */ }
    return null;
  }, []);

  const refreshDetail = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/jobs/${id}`, { cache: "no-store" });
      if (res.ok) {
        const data: JobDetail = await res.json();
        setDetail(data);
        if (data.files?.["transcript.json"]) {
          const t = await fetch(`/api/jobs/${id}/transcript`, { cache: "no-store" });
          if (t.ok) setTranscript(await t.json());
          else setTranscript(null);
        } else {
          setTranscript(null);
        }
      }
    } catch { /* transient */ }
  }, []);

  useEffect(() => {
    refreshJobs().then((list) => {
      if (list && list.length && !selected) setSelected(list[0].id);
    });
  }, [refreshJobs]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => {
      refreshJobs();
      if (selected) refreshDetail(selected);
    }, 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [selected, refreshJobs, refreshDetail]);

  useEffect(() => {
    if (selected) refreshDetail(selected);
    else setDetail(null);
  }, [selected, refreshDetail]);

  async function submitJob(v: SubmitFormValues) {
    setSubmitting(true);
    try {
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: v.url,
          accuracy: v.accuracy,
          language: v.language || undefined,
          excerpt: v.excerpt ? Number(v.excerpt) : undefined,
          mux: v.mux,
          terms: Object.fromEntries(v.terms.filter((t) => t.src && t.en).map((t) => [t.src, t.en])),
        }),
      });
      const data = await res.json();
      if (res.ok && data.job?.id) {
        setSelected(data.job.id);
        await refreshJobs();
      }
    } finally {
      setSubmitting(false);
    }
  }

  const status = detail ? STATUS_STYLE[detail.status] : null;
  const stages = detail?.report?.stages ?? [];
  const findings = detail?.report?.findings ?? [];

  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100">
      {/* Header */}
      <header className="border-b border-zinc-900 bg-zinc-950/90 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center gap-3">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600/15 border border-emerald-800/50">
              <Languages className="h-4.5 w-4.5 text-emerald-400" />
            </span>
            <div className="leading-tight">
              <h1 className="text-sm font-semibold tracking-tight">Universal Transcriptor</h1>
              <p className="text-[10px] text-zinc-500">Any-language media → professional English subtitles</p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Badge variant="outline" className="border-zinc-800 text-zinc-400 text-[10px] hidden sm:inline-flex">
              <CircleDot className="h-3 w-3 mr-1 text-emerald-500" /> engine online
            </Badge>
            <a
              href="https://github.com/Bilal140202/universal-transcriptor"
              target="_blank" rel="noreferrer"
              className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-900"
              aria-label="GitHub repository"
            >
              <Github className="h-4 w-4" />
            </a>
          </div>
        </div>
      </header>

      {/* Body */}
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-6">
        <div className="grid lg:grid-cols-[340px_1fr] gap-6 items-start">
          {/* Left column */}
          <div className="space-y-5 lg:sticky lg:top-20">
            <section className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
              <div className="flex items-center gap-2 mb-3.5">
                <Sparkles className="h-4 w-4 text-emerald-400" />
                <h2 className="text-sm font-semibold">New transcription job</h2>
              </div>
              <SubmitForm onSubmit={submitJob} submitting={submitting} />
            </section>

            <section className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
              <h2 className="text-sm font-semibold mb-2.5 flex items-center gap-2">
                <Activity className="h-4 w-4 text-emerald-400" /> Jobs
                <span className="text-xs text-zinc-600 font-normal">({jobs.length})</span>
              </h2>
              <ScrollArea className="h-44">
                <div className="space-y-1.5 pr-2">
                  {jobs.length === 0 && (
                    <p className="text-xs text-zinc-600 py-4 text-center">No jobs yet</p>
                  )}
                  {jobs.map((j) => (
                    <button
                      key={j.id}
                      onClick={() => setSelected(j.id)}
                      className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${
                        selected === j.id
                          ? "border-emerald-800/70 bg-emerald-950/20"
                          : "border-zinc-900 bg-zinc-900/30 hover:border-zinc-800"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                            j.status === "ok" || j.status === "partial" ? "bg-emerald-400"
                              : j.status === "running" ? "bg-amber-400"
                              : "bg-red-400"
                          }`}
                        />
                        <span className="text-xs text-zinc-200 truncate flex-1">{j.url}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 text-[10px] text-zinc-600">
                        <span>{j.created_at.slice(0, 16).replace("T", " ")}</span>
                        <span>· {j.accuracy}</span>
                        {j.excerpt ? <span>· first {j.excerpt}s</span> : null}
                        {j.findings > 0 && (
                          <span className="text-amber-500/80 flex items-center">
                            <ShieldAlert className="h-2.5 w-2.5 mr-0.5" />{j.findings}
                          </span>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </section>
          </div>

          {/* Right column — detail */}
          <div className="min-w-0">
            {!detail ? (
              <div className="rounded-xl border border-dashed border-zinc-900 bg-zinc-950/40 flex flex-col items-center justify-center py-24 text-center px-6">
                <Clapperboard className="h-10 w-10 text-zinc-700 mb-4" />
                <h2 className="text-base font-medium text-zinc-300">No job selected</h2>
                <p className="text-sm text-zinc-600 mt-1.5 max-w-md leading-relaxed">
                  Submit a Google Drive link, a direct media URL, or a local file on the left.
                  The engine acquires the real media, transcribes it in its original
                  language, translates with full context, and exports broadcast-style
                  English subtitles.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
                  <div className="flex items-start gap-3 flex-wrap">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-zinc-100 break-all">
                        {detail.meta.url}
                      </p>
                      <p className="text-[11px] text-zinc-500 mt-0.5">
                        job {detail.meta.id} · accuracy {detail.meta.accuracy}
                        {detail.meta.language ? ` · lang ${detail.meta.language}` : " · lang auto"}
                        {detail.meta.excerpt ? ` · excerpt ${detail.meta.excerpt}s` : ""}
                      </p>
                    </div>
                    {status && (
                      <Badge variant="outline" className={`${status.cls} text-xs font-medium`}>
                        {detail.status === "running" && (
                          <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                        )}
                        {status.label}
                      </Badge>
                    )}
                  </div>
                </div>

                <Tabs defaultValue="pipeline">
                  <TabsList className="bg-zinc-900 border border-zinc-800 h-9">
                    <TabsTrigger value="pipeline" className="text-xs data-[state=active]:text-emerald-300">
                      Pipeline
                    </TabsTrigger>
                    <TabsTrigger value="review" className="text-xs data-[state=active]:text-emerald-300">
                      Review
                    </TabsTrigger>
                    <TabsTrigger value="findings" className="text-xs data-[state=active]:text-emerald-300">
                      QA findings {findings.length > 0 && `(${findings.length})`}
                    </TabsTrigger>
                    <TabsTrigger value="export" className="text-xs data-[state=active]:text-emerald-300">
                      Export
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value="pipeline" className="mt-4">
                    <div className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
                      <StageTimeline stages={stages} />
                    </div>
                  </TabsContent>
                  <TabsContent value="review" className="mt-4">
                    <div className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
                      <ReviewPanel
                        job={detail}
                        transcript={transcript}
                        onSaved={() => refreshDetail(detail.meta.id)}
                      />
                    </div>
                  </TabsContent>
                  <TabsContent value="findings" className="mt-4">
                    <div className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
                      <FindingsPanel findings={findings} />
                    </div>
                  </TabsContent>
                  <TabsContent value="export" className="mt-4">
                    <div className="rounded-xl border border-zinc-900 bg-zinc-950/60 p-4">
                      <ExportPanel job={detail} />
                    </div>
                  </TabsContent>
                </Tabs>
              </div>
            )}
          </div>
        </div>
      </main>

      {/* Sticky footer */}
      <footer className="mt-auto border-t border-zinc-900 bg-zinc-950">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-11 flex items-center justify-between text-[11px] text-zinc-600">
          <p>Staged · verified · provider-abstracted — never a naive Whisper pipeline.</p>
          <p className="font-mono">acquisition → probe → ASR → align → translate → QA → subtitles</p>
        </div>
      </footer>
    </div>
  );
}
