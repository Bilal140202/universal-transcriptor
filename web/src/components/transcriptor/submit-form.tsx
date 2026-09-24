"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Loader2, Plus, Sparkles, X } from "lucide-react";

export interface SubmitFormValues {
  url: string;
  accuracy: "best" | "balanced" | "fast";
  language: string;
  excerpt: string;
  mux: boolean;
  terms: Array<{ src: string; en: string }>;
}

const ACCURACY_HINT: Record<string, string> = {
  best: "large-v3 — maximum accuracy, slowest",
  balanced: "base (CPU) / distil-large (GPU) — recommended",
  fast: "small — quick previews and drafts",
};

export function SubmitForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (v: SubmitFormValues) => void;
  submitting: boolean;
}) {
  const [url, setUrl] = useState("");
  const [accuracy, setAccuracy] = useState<"best" | "balanced" | "fast">("balanced");
  const [language, setLanguage] = useState("auto");
  const [excerpt, setExcerpt] = useState("");
  const [mux, setMux] = useState(false);
  const [terms, setTerms] = useState<Array<{ src: string; en: string }>>([]);

  const valid = /^https?:\/\//i.test(url.trim()) || url.trim().startsWith("/");

  function submit() {
    if (!valid) return;
    onSubmit({
      url: url.trim(),
      accuracy,
      language: language === "auto" ? "" : language,
      excerpt: excerpt.trim(),
      mux,
      terms,
    });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="src-url" className="text-zinc-300 text-xs font-medium uppercase tracking-wide">
          Media source
        </Label>
        <Input
          id="src-url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://drive.google.com/file/d/…  ·  direct URL  ·  /path/file.mp4"
          className="bg-zinc-900 border-zinc-800 text-zinc-100 placeholder:text-zinc-600 h-10 text-sm"
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <p className="text-[11px] text-zinc-500 leading-relaxed">
          Google Drive share links (incl. virus-scan &amp; quota interstitials), direct
          HTTP with resume, or a local path. Auto-detected adapter per source.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label className="text-zinc-300 text-xs font-medium uppercase tracking-wide">Accuracy</Label>
          <Select value={accuracy} onValueChange={(v) => setAccuracy(v as typeof accuracy)}>
            <SelectTrigger className="bg-zinc-900 border-zinc-800 text-zinc-100 h-10 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-zinc-900 border-zinc-800 text-zinc-100">
              <SelectItem value="best">Best</SelectItem>
              <SelectItem value="balanced">Balanced</SelectItem>
              <SelectItem value="fast">Fast</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[11px] text-zinc-500">{ACCURACY_HINT[accuracy]}</p>
        </div>
        <div className="space-y-2">
          <Label className="text-zinc-300 text-xs font-medium uppercase tracking-wide">Language</Label>
          <Select value={language} onValueChange={setLanguage}>
            <SelectTrigger className="bg-zinc-900 border-zinc-800 text-zinc-100 h-10 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-zinc-900 border-zinc-800 text-zinc-100 max-h-64">
              <SelectItem value="auto">Auto-detect</SelectItem>
              {["ja", "ko", "zh", "en", "es", "fr", "de", "ru", "ar", "hi", "ur",
                "th", "vi", "id", "tr", "pt", "it"].map((l) => (
                <SelectItem key={l} value={l}>{l.toUpperCase()}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[11px] text-zinc-500">Whisper covers 99 languages</p>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="excerpt" className="text-zinc-300 text-xs font-medium uppercase tracking-wide">
          Excerpt (seconds, optional)
        </Label>
        <Input
          id="excerpt"
          value={excerpt}
          onChange={(e) => setExcerpt(e.target.value.replace(/[^0-9.]/g, ""))}
          placeholder="e.g. 240 — first N seconds only (prefix cut, timestamps stay 1:1)"
          className="bg-zinc-900 border-zinc-800 text-zinc-100 placeholder:text-zinc-600 h-10 text-sm"
        />
      </div>

      <div className="space-y-2">
        <Label className="text-zinc-300 text-xs font-medium uppercase tracking-wide">
          Terminology overrides
        </Label>
        {terms.map((t, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={t.src}
              onChange={(e) => setTerms(terms.map((x, j) => j === i ? { ...x, src: e.target.value } : x))}
              placeholder="source term"
              className="bg-zinc-900 border-zinc-800 text-zinc-100 h-9 text-sm"
            />
            <span className="text-zinc-600">→</span>
            <Input
              value={t.en}
              onChange={(e) => setTerms(terms.map((x, j) => j === i ? { ...x, en: e.target.value } : x))}
              placeholder="English term"
              className="bg-zinc-900 border-zinc-800 text-zinc-100 h-9 text-sm"
            />
            <Button
              variant="ghost" size="icon"
              onClick={() => setTerms(terms.filter((_, j) => j !== i))}
              className="text-zinc-500 hover:text-zinc-200 h-9 w-9 shrink-0"
              aria-label={`remove term ${i + 1}`}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button
          variant="outline" size="sm"
          onClick={() => setTerms([...terms, { src: "", en: "" }])}
          className="border-zinc-800 bg-zinc-900 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 text-xs"
        >
          <Plus className="h-3.5 w-3.5 mr-1" /> Add term (e.g. YG → YG)
        </Button>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
        <div>
          <p className="text-sm text-zinc-200">Attach soft subtitle track</p>
          <p className="text-[11px] text-zinc-500">Stream copy — the video is never re-encoded</p>
        </div>
        <Switch checked={mux} onCheckedChange={setMux} />
      </div>

      <Button
        onClick={submit}
        disabled={!valid || submitting}
        className="w-full h-11 bg-emerald-600 hover:bg-emerald-500 text-white font-medium"
      >
        {submitting ? (
          <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Starting pipeline…</>
        ) : (
          <><Sparkles className="h-4 w-4 mr-2" /> Transcribe &amp; translate</>
        )}
      </Button>
    </div>
  );
}
