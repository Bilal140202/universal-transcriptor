"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, MonitorPlay, Pencil, Save, VideoOff } from "lucide-react";
import { fmtTime, type JobDetail, type TranscriptView } from "@/lib/types";

export function ReviewPanel({
  job, transcript, onSaved,
}: {
  job: JobDetail;
  transcript: TranscriptView | null;
  onSaved: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [videoTime, setVideoTime] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => setDrafts({}), [transcript === null]);

  const segs = useMemo(() => transcript?.segments ?? [], [transcript]);

  // active cue highlight while playing
  const activeIdx = useMemo(() => {
    for (const s of segs) {
      if (videoTime >= s.start - 0.05 && videoTime <= s.end + 0.05) {
        return s.segment_id;
      }
    }
    return null;
  }, [videoTime, segs]);

  function seekTo(start: number) {
    const v = videoRef.current;
    if (!v || !job.preview || job.preview !== "ready") return;
    v.currentTime = start;
    v.play().catch(() => {});
  }

  async function saveEdits() {
    if (!Object.keys(drafts).length) return;
    setSaving(true);
    try {
      const res = await fetch(`/api/jobs/${job.meta.id}/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edits: drafts }),
      });
      if (res.ok) {
        setDrafts({});
        onSaved();
      }
    } finally {
      setSaving(false);
    }
  }

  const dirty = Object.keys(drafts).length;

  return (
    <div className="space-y-5">
      {/* Video preview */}
      <div className="rounded-xl border border-zinc-800 bg-black/60 overflow-hidden">
        {job.preview === "ready" ? (
          <video
            ref={videoRef}
            controls
            className="w-full aspect-video bg-black"
            src={`/api/media?job=${job.meta.id}&preview=1`}
            onTimeUpdate={(e) => setVideoTime(e.currentTarget.currentTime)}
          >
            <track
              kind="subtitles" srcLang="en" label="English"
              src={`/api/jobs/${job.meta.id}/download?fmt=vtt`}
              default
            />
          </video>
        ) : job.preview === "generating" ? (
          <div className="aspect-video flex flex-col items-center justify-center gap-3 text-zinc-400">
            <Loader2 className="h-8 w-8 animate-spin text-emerald-400" />
            <p className="text-sm">Generating lightweight review preview…</p>
            <p className="text-xs text-zinc-600">The original file is never re-encoded — this clip is only for in-browser playback.</p>
          </div>
        ) : (
          <div className="aspect-video flex flex-col items-center justify-center gap-3 text-zinc-500">
            <VideoOff className="h-8 w-8" />
            <p className="text-sm">Preview unavailable for this run</p>
          </div>
        )}
      </div>

      {/* Segment editor */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MonitorPlay className="h-4 w-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-zinc-100">
            Review segments
            {transcript?.language && (
              <Badge variant="outline" className="ml-2 border-zinc-700 text-zinc-400 uppercase">
                {transcript.language} → en
              </Badge>
            )}
          </h3>
        </div>
        <Button
          size="sm"
          onClick={saveEdits}
          disabled={!dirty || saving}
          className="bg-emerald-600 hover:bg-emerald-500 text-white h-8 text-xs"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5 mr-1.5" />
          )}
          {dirty ? `Re-export ${dirty} edit${dirty > 1 ? "s" : ""}` : "No edits"}
        </Button>
      </div>

      <div
        ref={listRef}
        className="space-y-2 max-h-[30rem] overflow-y-auto pr-1 custom-scroll"
      >
        {!transcript || segs.length === 0 ? (
          <p className="text-sm text-zinc-500 text-center py-10">
            Transcript appears here once the pipeline finishes.
          </p>
        ) : (
          segs.map((s) => {
            const draft = drafts[s.segment_id];
            const isEdited = s.edited || draft != null;
            const isActive = activeIdx === s.segment_id;
            return (
              <div
                key={s.segment_id}
                className={`rounded-lg border px-3 py-2.5 transition-colors ${
                  isActive
                    ? "border-emerald-700/70 bg-emerald-950/20"
                    : "border-zinc-800/80 bg-zinc-900/40"
                }`}
              >
                <div className="flex items-center gap-2 flex-wrap mb-1.5">
                  <button
                    onClick={() => seekTo(s.start)}
                    className="text-[11px] font-mono text-emerald-400/90 hover:text-emerald-300 tabular-nums"
                    title="seek video here"
                  >
                    {fmtTime(s.start)} → {fmtTime(s.end)}
                  </button>
                  {s.speaker && (
                    <Badge variant="outline" className="border-zinc-700 text-zinc-400 text-[10px]">
                      {s.speaker}
                    </Badge>
                  )}
                  {isEdited ? (
                    <Badge className="bg-amber-900/60 text-amber-300 border-amber-800 text-[10px]">
                      <Pencil className="h-2.5 w-2.5 mr-1" /> edited
                    </Badge>
                  ) : null}
                  {s.confidence < 0.55 && (
                    <Badge variant="outline" className="border-amber-800 text-amber-400 text-[10px]">
                      low conf {s.confidence.toFixed(2)}
                    </Badge>
                  )}
                  <span className="text-[10px] text-zinc-600 font-mono ml-auto">{s.segment_id}</span>
                </div>
                <p className="text-[13px] text-zinc-400 leading-snug mb-2" dir="auto">
                  {s.text}
                </p>
                <Textarea
                  value={draft ?? s.english ?? ""}
                  onChange={(e) =>
                    setDrafts((d) => ({ ...d, [s.segment_id]: e.target.value }))}
                  rows={Math.min(3, Math.ceil(((draft ?? s.english ?? "").length || 1) / 60))}
                  className="bg-zinc-950/70 border-zinc-800 text-zinc-100 text-sm min-h-[2.4rem] resize-y"
                  placeholder="English translation…"
                  dir="ltr"
                />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
